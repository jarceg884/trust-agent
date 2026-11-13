"""Spec 040 — Instaladores desktop por plataforma: página, endpoints
/download/{slug}/installer/{platform}, audit por plataforma, estados honestos
(REQ-PI-001..004, REQ-PI-008, REQ-PI-009).

Decisión de producto vigente (Miller): las descargas están limitadas a
Windows; macOS y Linux se anuncian como "coming soon" — la página no los
enlaza y el endpoint responde 404 aunque exista un build en el manifest. Por
eso el serving, el versionado y la seguridad pública se prueban sobre Windows.

Los artefactos reales los construye el HOST (scripts/build_desktop_installers.py);
aquí se verifica el SERVING: el directorio de artefactos se apunta a un tmp
con un manifest + binario de prueba (el contrato que el script escribe).
"""
import hashlib
import json

import pytest

from conftest import (
    DEVICE_ALICE,
    TENANT_A,
    TENANT_B,
    TEST_DB,
    USER_ALICE,
    _admin,
)
from test_hermes_download import (
    _audit_count,
    _ip_client,
    _set_tenant_state,
)

COMING_SOON = "coming soon"


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
    """Apunta settings.installer_artifacts_dir a un tmp (layout del script).

    Vacío por defecto: sin builds ni fallback ``_universal`` — cada test
    declara exactamente lo que existe (hermético frente al host).
    """
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
    import re

    from app.config import get_settings

    plugin = os.path.join(get_settings().hermes_plugin_dir, "plugin.yaml")
    match = re.search(r"^version:\s*(\S+)", open(plugin).read(), re.MULTILINE)
    assert match is not None
    return match.group(1)


# --- REQ-PI-001: página — Windows + "macOS & Linux coming soon" ----------------

def test_page_lists_windows_and_coming_soon(client, artifacts_dir):
    r = client.get("/download/tenant-a")
    assert r.status_code == 200
    body = r.text
    assert 'data-os="windows"' in body
    # macOS y Linux: una sola tarjeta "coming soon", sin enlace de descarga.
    assert 'data-os="macos-linux"' in body
    assert "Coming soon." in body
    assert "limited to Windows" in body
    assert "Download for your platform" in body
    assert "navigator.userAgent" in body  # OS detect client-side (D-040-06)
    # La sección CLI sigue (spec 034, REQ-034-003).
    assert "/download/tenant-a/cli" in body
    assert "curl -fsSL" in body


def test_page_buttons_honest_without_builds(client, artifacts_dir):
    """Sin build servido: ningún botón enlaza a un artefacto inexistente
    (REQ-PI-002/009) — la tarjeta Windows dice que se está publicando."""
    body = client.get("/download/tenant-a").text
    assert "/download/tenant-a/installer/" not in body
    assert "being published" in body


def test_page_buttons_enabled_with_build(client, artifacts_dir):
    _bake_build(artifacts_dir, "tenant-a", _plugin_version(),
                {"windows": b"fake-exe", "linux": b"fake-appimage",
                 "macos": b"fake-dmg"})
    body = client.get("/download/tenant-a").text
    assert "/download/tenant-a/installer/windows" in body
    assert "Detected for your system" in body  # badge en la card con build
    # Un build de Linux/macOS NO habilita descarga mientras sean coming soon.
    assert "/download/tenant-a/installer/linux" not in body
    assert "/download/tenant-a/installer/macos" not in body


# --- REQ-PI-003: serving real + audit por plataforma ---------------------------

def test_installer_served_and_audited(client, artifacts_dir):
    _bake_build(artifacts_dir, "tenant-a", _plugin_version(),
                {"windows": b"fake-msix-payload"})
    c = _ip_client(client, "198.51.100.110")
    before = _audit_count("download.issued")
    r = c.get("/download/tenant-a/installer/windows")
    assert r.status_code == 200, r.text
    assert r.content == b"fake-msix-payload"
    assert r.headers["X-Installer-SHA256"] == hashlib.sha256(
        b"fake-msix-payload").hexdigest()
    assert "attachment" in r.headers["content-disposition"]

    # Audit download.issued artifact=installer-windows (D-040-08).
    with _admin(TEST_DB) as conn:
        row = conn.execute(
            "SELECT payload->>'artifact', payload->>'platform', "
            "payload->>'plugin_version' FROM audit_log "
            "WHERE tenant_id = %s AND action = 'download.issued' "
            "ORDER BY seq DESC LIMIT 1",
            (TENANT_A,),
        ).fetchone()
    assert row[0] == "installer-windows"
    assert row[1] == "windows"
    assert row[2] == _plugin_version()  # versionado por release (REQ-PI-008)
    assert _audit_count("download.issued") == before + 1


@pytest.mark.parametrize("platform", ["macos", "linux"])
def test_macos_and_linux_are_coming_soon_even_with_a_build(client, artifacts_dir, platform):
    """Decisión de producto: solo Windows. Ni un build en el manifest habilita
    macOS/Linux — 404 honesto con el mensaje coming soon y sin audit."""
    _bake_build(artifacts_dir, "tenant-a", _plugin_version(), {platform: b"x"})
    c = _ip_client(client, "198.51.100.112")
    before = _audit_count("download.issued")
    r = c.get(f"/download/tenant-a/installer/{platform}")
    assert r.status_code == 404
    assert COMING_SOON in r.json()["error"]["message"]
    assert _audit_count("download.issued") == before


def test_unknown_platform_404(client):
    r = client.get("/download/tenant-a/installer/beos")
    assert r.status_code == 404


def test_old_plugin_version_not_served(client, artifacts_dir):
    """D-040-07: solo se sirve el build de la versión del plugin ACTUAL."""
    _bake_build(artifacts_dir, "tenant-a", "0.0.1-ancient",
                {"windows": b"stale-build"})
    c = _ip_client(client, "198.51.100.113")
    r = c.get("/download/tenant-a/installer/windows")
    assert r.status_code == 404
    assert r.json()["error"]["reason"] == "installer_not_built"
    # Y la página no habilita el botón con un build viejo.
    assert "/download/tenant-a/installer/windows" not in client.get(
        "/download/tenant-a").text


def test_missing_artifact_file_not_served(client, artifacts_dir):
    """El manifest solo no basta: el binario debe existir (honestidad)."""
    d = artifacts_dir / "tenant-a" / _plugin_version()
    d.mkdir(parents=True)
    (d / "installer-manifest.json").write_text(json.dumps({
        "slug": "tenant-a",
        "plugin_version": _plugin_version(),
        "platforms": {"windows": {"filename": "ghost.bin",
                                  "sha256": "0" * 64, "size": 1}},
    }))
    c = _ip_client(client, "198.51.100.114")
    r = c.get("/download/tenant-a/installer/windows")
    assert r.status_code == 404
    assert r.json()["error"]["reason"] == "installer_not_built"


# --- REQ-PI-004: seguridad pública heredada del bundle -------------------------

def test_installer_rate_limited(client, artifacts_dir):
    _bake_build(artifacts_dir, "tenant-a", _plugin_version(),
                {"windows": b"x"})
    c = _ip_client(client, "198.51.100.115")
    for _ in range(5):  # download_bundle_rate_max = 5
        assert c.get("/download/tenant-a/installer/windows").status_code == 200
    r = c.get("/download/tenant-a/installer/windows")
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


def test_installer_unknown_slug_anti_enumeration(client):
    c = _ip_client(client, "198.51.100.116")
    for _ in range(5):
        assert c.get("/download/no-such-org/installer/windows").status_code == 404
    assert c.get("/download/no-such-org/installer/windows").status_code == 429


def test_installer_fail_closed(client, artifacts_dir):
    _bake_build(artifacts_dir, "tenant-b", _plugin_version(),
                {"windows": b"x"})
    c = _ip_client(client, "198.51.100.117")
    # Link deshabilitado → 403 link_disabled.
    _set_tenant_state(TENANT_B, download_enabled=False)
    r = c.get("/download/tenant-b/installer/windows")
    assert r.status_code == 403
    assert r.json()["error"]["reason"] == "link_disabled"
    # Tenant suspendido → 403 tenant_suspended (fail-closed).
    _set_tenant_state(TENANT_B, download_enabled=True, status="suspended")
    r = c.get("/download/tenant-b/installer/windows")
    assert r.status_code == 403
    assert r.json()["error"]["reason"] == "tenant_suspended"


def test_installer_isolation_per_tenant(client, artifacts_dir):
    """El manifest de B no habilita nada en A (aislamiento)."""
    _bake_build(artifacts_dir, "tenant-b", _plugin_version(),
                {"windows": b"tenant-b-build"})
    c = _ip_client(client, "198.51.100.118")
    assert c.get("/download/tenant-a/installer/windows").status_code == 404
    assert c.get("/download/tenant-b/installer/windows").status_code == 200


# --- Public edge (console :3100, 2026-11-12) ------------------------------------
# A governed desktop outside the LAN asks for updates through the console's public edge,
# so the API sees http://127.0.0.1:8000: the advertised download URL must be the public
# origin (TRUST_PUBLIC_API_BASE_URL), never the edge's loopback upstream.

def test_update_check_download_url_is_the_public_origin(client, artifacts_dir, monkeypatch):
    from app.config import get_settings
    from app.devices.tokens import issue_device_token

    version = _plugin_version()
    _bake_build(artifacts_dir, "tenant-a", version, {"windows": b"MSIX-PAYLOAD"})
    manifest_file = artifacts_dir / "tenant-a" / version / "installer-manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["git_rev"] = "rev-published"
    manifest_file.write_text(json.dumps(manifest))

    def update_check():
        token = issue_device_token(
            get_settings(), device_id=DEVICE_ALICE, user_id=USER_ALICE, tenant_id=TENANT_A
        )
        r = client.get(
            "/api/me/update-check",
            params={"current_git_rev": "rev-installed", "platform": "windows"},
            # Plugin path (same auth as the governance bundle): Bearer = device token.
            headers={"Authorization": f"Bearer {token}", "X-Device-Token": token},
        )
        assert r.status_code == 200, r.text
        assert r.json()["update_available"] is True
        return r.json()["download_url"]

    # Direct access (default): the origin the desktop used.
    assert update_check() == f"{str(client.base_url).rstrip('/')}/download/tenant-a/installer/windows"

    monkeypatch.setenv("TRUST_PUBLIC_API_BASE_URL", "http://203.0.113.10:3100")
    get_settings.cache_clear()
    assert update_check() == "http://203.0.113.10:3100/download/tenant-a/installer/windows"



def test_update_check_offers_the_universal_build_to_every_tenant(client, artifacts_dir):
    """A tenant without its own build is served the universal MSIX: its update check must read
    that same manifest (it never offered anything before), comparing by commit identity."""
    from app.config import get_settings
    from app.devices.tokens import issue_device_token

    version = _plugin_version()
    _bake_build(artifacts_dir, "_universal", version, {"windows": b"UNIVERSAL-MSIX"})
    manifest_file = artifacts_dir / "_universal" / version / "installer-manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest["git_rev"] = "1f751d5c2451aa00bb11cc22dd33ee44ff556677"
    manifest_file.write_text(json.dumps(manifest))

    def update_check(current: str) -> dict:
        token = issue_device_token(
            get_settings(), device_id=DEVICE_ALICE, user_id=USER_ALICE, tenant_id=TENANT_A
        )
        r = client.get(
            "/api/me/update-check",
            params={"current_git_rev": current, "platform": "windows"},
            headers={"Authorization": f"Bearer {token}", "X-Device-Token": token},
        )
        assert r.status_code == 200, r.text
        return r.json()

    older = update_check("c895221")
    assert older["update_available"] is True
    assert older["published_version"] == version
    assert older["download_url"].endswith("/download/tenant-a/installer/windows")
    assert older["sha256"] == hashlib.sha256(b"UNIVERSAL-MSIX").hexdigest()
    # The desktop sends a 7-char stamp; the manifest keeps the full commit: same build.
    assert update_check("1f751d5")["update_available"] is False
    assert update_check("1F751D5C2451")["update_available"] is False


def test_same_build_needs_a_real_prefix():
    from app.download.installers import same_build

    assert same_build("1f751d5c2451aa00", "1f751d5")
    assert not same_build("1f751d5c2451aa00", "1f75")  # too short to identify a build
    assert not same_build("1f751d5c2451aa00", "")
    assert not same_build("", "1f751d5")
    assert same_build("rev-a", "rev-a")

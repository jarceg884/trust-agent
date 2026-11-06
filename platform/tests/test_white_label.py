"""Spec 042 REQ-PE-003 — white-label total: el empleado NUNCA ve "Hermes" en
textos visibles del wizard, install.sh generado, instalador CLI generado y
README del bundle. Alias de comando trust-agent presente en los instaladores.

AC-003-1: grep case-insensitive de "hermes" en textos visibles → 0 resultados
          (excluye rutas internas ~/.hermes, env vars HERMES_HOME y el comando
          del binario core `hermes` en sentencias no-echo — documentado).
AC-003-2: el instalador instala un wrapper `trust-agent` en ~/.local/bin.
AC-003-3: la TUI muestra agent_name = product_name (skin _skin_yaml).
"""
import io
import re
import tarfile

import pytest
from fastapi.testclient import TestClient

from app.download.bundle import build_bundle, _skin_yaml, _README
from app.download.cli import build_cli_installer

from conftest import (
    TENANT_A,
    TEST_DB,
    _admin,
)


def _ip_client(client, ip):
    return TestClient(client.app, client=(ip, 54321))


@pytest.fixture
def _pristine_db(db):
    """Reset tenant state for the page test (needs the session-scoped db)."""
    def reset():
        with _admin(TEST_DB) as c:
            c.execute(
                "UPDATE tenants SET status = 'active', download_enabled = true "
                "WHERE id = %s",
                (TENANT_A,),
            )
            c.execute("DELETE FROM ops.login_throttle WHERE key LIKE 'dl:%'")
    reset()
    yield
    reset()


# --- Helpers: extraer solo texto visible (echo/heredoc) de un script bash -----

def _visible_lines(script: str) -> list[str]:
    """Líneas que el empleado ve en pantalla: echo, heredoc bodies que van a
    stdout (cat <<NEXT, no cat > file <<EOF). Excluye sentencias
    `hermes config set`, rutas ~/.hermes, env vars HERMES_HOME y
    asignaciones internas."""
    visible = []
    in_heredoc = False
    heredoc_end = None
    heredoc_to_file = False
    for line in script.splitlines():
        # Detect heredoc start (cat <<'NEXT' / cat <<NEXT / cat > file <<'EOF')
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        if m and not in_heredoc:
            in_heredoc = True
            heredoc_end = m.group(1)
            # If the line has a redirect (>), it writes to a file, not stdout.
            heredoc_to_file = ">" in line[:m.start()]
            continue
        if in_heredoc:
            if line.strip() == heredoc_end:
                in_heredoc = False
                heredoc_end = None
                heredoc_to_file = False
                continue
            if not heredoc_to_file:
                visible.append(line)
            continue
        # echo lines (visible to employee)
        stripped = line.lstrip()
        if stripped.startswith("echo ") or stripped.startswith('echo "'):
            visible.append(line)
    return visible


def _visible_hermes_hits(text: str) -> list[str]:
    """Case-insensitive grep de 'hermes' en texto visible, excluyendo
    rutas internas (~/.hermes), env vars (HERMES_HOME) y el comando del
    binario core en sentencias no-echo."""
    hits = []
    for line in text.splitlines():
        lower = line.lower()
        if "hermes" not in lower:
            continue
        # Exclusiones: rutas internas y env vars del core (no son texto visible).
        # Estas aparecen en asignaciones, export, ${HERMES_HOME}, ~/.hermes.
        cleaned = re.sub(r'\$?\{?HERMES_HOME\}?', '', line, flags=re.IGNORECASE)
        cleaned = re.sub(r'~/\.hermes', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\.hermes', '', cleaned, flags=re.IGNORECASE)
        # El comando `hermes` en `hermes config set` o `command -v hermes`
        # es el binario core, no texto visible.
        cleaned = re.sub(r'\bhermes\b(?=\s+(?:config|config|--version))', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'command -v hermes', '', cleaned, flags=re.IGNORECASE)
        if "hermes" in cleaned.lower():
            hits.append(line)
    return hits


BRANDING = {
    "product_name": "Acme Agent",
    "tagline": "Acme governed AI",
    "accent_color": "#c90104",
}


# --- AC-003-1: sin "Hermes" en textos visibles --------------------------------


def test_install_sh_no_visible_hermes():
    """El install.sh generado no contiene 'Hermes' en líneas visibles
    (echo + heredoc). Las rutas internas ~/.hermes y HERMES_HOME se excluyen."""
    payload, _, _ = build_bundle(
        slug="tenant-a",
        api_base="https://download.test",
        branding=BRANDING,
        litellm_base_url="http://gateway.test",
        oidc_issuer_url="https://idp.test",
        oidc_client_id="test-client",
    )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        install = tar.extractfile("trust-agent-tenant-a/install.sh").read().decode()

    visible = _visible_lines(install)
    hits = _visible_hermes_hits("\n".join(visible))
    assert hits == [], f"Hermes visible en install.sh: {hits}"


def test_cli_installer_no_visible_hermes():
    """El instalador CLI generado no contiene 'Hermes' en líneas visibles."""
    script = build_cli_installer(
        slug="tenant-a",
        api_base="https://download.test",
        litellm_base_url="http://gateway.test",
        model="glm-5.2",
        product_name="Acme Agent",
    ).decode()

    visible = _visible_lines(script)
    hits = _visible_hermes_hits("\n".join(visible))
    assert hits == [], f"Hermes visible en CLI installer: {hits}"


def test_readme_no_hermes():
    """El README del bundle no contiene 'Hermes' (caso insensible)."""
    payload, _, _ = build_bundle(
        slug="tenant-a",
        api_base="https://download.test",
        branding=BRANDING,
        litellm_base_url="http://gateway.test",
        oidc_issuer_url="https://idp.test",
        oidc_client_id="test-client",
    )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        readme = tar.extractfile("trust-agent-tenant-a/README.md").read().decode()

    assert "hermes" not in readme.lower(), f"Hermes en README: {[l for l in readme.splitlines() if 'hermes' in l.lower()]}"


def test_wizard_renderer_no_hermes():
    """El HTML del renderer del wizard no contiene 'Hermes'."""
    import pathlib
    renderer = pathlib.Path(__file__).parent.parent / "installer-app" / "renderer" / "index.html"
    html = renderer.read_text()
    assert "hermes" not in html.lower(), "Hermes en renderer/index.html"


def test_download_page_no_hermes(client, _pristine_db):
    """La página de descarga pública no contiene 'Hermes' en texto visible."""
    c = _ip_client(client, "198.51.100.231")
    r = c.get("/download/tenant-a")
    assert r.status_code == 200
    assert "hermes" not in r.text.lower(), "Hermes en página de descarga"


# --- AC-003-2: alias trust-agent en los instaladores --------------------------


def test_install_sh_installs_trust_agent_alias():
    """El install.sh instala un wrapper `trust-agent` en ~/.local/bin."""
    payload, _, _ = build_bundle(
        slug="tenant-a",
        api_base="https://download.test",
        branding=BRANDING,
        litellm_base_url="http://gateway.test",
        oidc_issuer_url="https://idp.test",
        oidc_client_id="test-client",
    )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        install = tar.extractfile("trust-agent-tenant-a/install.sh").read().decode()

    assert "trust-agent" in install
    assert ".local/bin" in install
    assert "exec hermes" in install  # wrapper invoca al binario oficial


def test_cli_installer_installs_trust_agent_alias():
    """El instalador CLI instala un wrapper `trust-agent` en ~/.local/bin."""
    script = build_cli_installer(
        slug="tenant-a",
        api_base="https://download.test",
        litellm_base_url="http://gateway.test",
        model="glm-5.2",
        product_name="Acme Agent",
    ).decode()

    assert "trust-agent" in script
    assert ".local/bin" in script
    assert "exec hermes" in script


def test_install_sh_shows_trust_agent_as_command():
    """El install.sh muestra `trust-agent` (no `hermes`) como comando al empleado."""
    payload, _, _ = build_bundle(
        slug="tenant-a",
        api_base="https://download.test",
        branding=BRANDING,
        litellm_base_url="http://gateway.test",
        oidc_issuer_url="https://idp.test",
        oidc_client_id="test-client",
    )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        install = tar.extractfile("trust-agent-tenant-a/install.sh").read().decode()

    # El heredoc "Next steps" debe decir trust-agent, no hermes.
    assert "Start the agent:\n       trust-agent" in install


def test_cli_installer_shows_trust_agent_as_command():
    """El CLI installer muestra `trust-agent` (no `hermes`) como comando."""
    script = build_cli_installer(
        slug="tenant-a",
        api_base="https://download.test",
        litellm_base_url="http://gateway.test",
        model="glm-5.2",
        product_name="Acme Agent",
    ).decode()

    assert "Start the agent:\n       trust-agent" in script


def test_readme_shows_trust_agent_as_command():
    """El README muestra `trust-agent` (no `hermes`) como comando."""
    payload, _, _ = build_bundle(
        slug="tenant-a",
        api_base="https://download.test",
        branding=BRANDING,
        litellm_base_url="http://gateway.test",
        oidc_issuer_url="https://idp.test",
        oidc_client_id="test-client",
    )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        readme = tar.extractfile("trust-agent-tenant-a/README.md").read().decode()

    assert "trust-agent" in readme
    # La sección Run debe decir trust-agent.
    run_section = readme[readme.index("## Run"):]
    assert "trust-agent" in run_section


# --- AC-003-3: skin muestra agent_name = product_name -------------------------


def test_skin_agent_name_is_product_name():
    """El skin white-label muestra agent_name = product_name del branding."""
    skin = _skin_yaml("tenant-a", BRANDING)
    assert "agent_name: Acme Agent" in skin


def test_product_name_in_install_sh_echo():
    """El install.sh usa PRODUCT_NAME en los mensajes visibles (no 'Hermes')."""
    payload, _, _ = build_bundle(
        slug="tenant-a",
        api_base="https://download.test",
        branding=BRANDING,
        litellm_base_url="http://gateway.test",
        oidc_issuer_url="https://idp.test",
        oidc_client_id="test-client",
    )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        install = tar.extractfile("trust-agent-tenant-a/install.sh").read().decode()

    assert "Acme Agent" in install  # product_name horneado
    assert 'PRODUCT_NAME="Acme Agent"' in install


def test_cli_installer_uses_product_name():
    """El CLI installer usa PRODUCT_NAME en los mensajes visibles."""
    script = build_cli_installer(
        slug="tenant-a",
        api_base="https://download.test",
        litellm_base_url="http://gateway.test",
        model="glm-5.2",
        product_name="Acme Agent",
    ).decode()

    assert 'PRODUCT_NAME="Acme Agent"' in script
    assert "${PRODUCT_NAME}" in script

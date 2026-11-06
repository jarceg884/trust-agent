"""Instalador CLI por tenant (spec 034, REQ-034-001, D-034-01/02/03).

``build_cli_installer`` genera el script bash servido por
``GET /download/{slug}/cli`` — el artefacto del mandato Miller para el
empleado que trabaja en un servidor/VPS/terminal:

    curl -fsSL <origin>/download/{slug}/cli | bash

El script hornea el ``org_code`` del tenant (identificador público,
D-023-02 — la seguridad del enrolamiento es la aprobación admin, spec 007)
y reusa el BUNDLE canónico como fuente única del plugin, ``trust-login`` y
el skin white-label (D-034-02): lo descarga del mismo origen y extrae esos
miembros en vez de duplicar el árbol. Instala:

  1. Hermes CLI vía el instalador oficial si no está presente (D-023-04,
     mismos targets oficiales: Linux/macOS/WSL2).
  2. Plugin ``trust-agent-governance`` (core de Hermes — cero parches).
  3. Binding de la organización (``org-code.txt``) + ``.env`` con
     ``TRUST_AGENT_API``/``TRUST_ORG_CODE``/``TRUST_DEVICE_TYPE=cli``.
  4. Skin white-label + config del agente (gateway standalone, plugin,
     toolset, modelo del tenant) — idéntico al desktop salvo el tipo.

Sin secretos (D-034-08): el id_token lo produce ``trust-login`` (OIDC
code+PKCE) DESPUÉS de instalar; la virtual key se auto-emite con
``--issue-key``. El enrolamiento es el MISMO flujo zero-UI del desktop
(spec 007/022): pending → aprobación admin → device token.
"""
from __future__ import annotations

from typing import Any

_CLI_INSTALL_SH = r"""#!/usr/bin/env bash
# install-cli.sh — Trust Agent CLI installer (spec 034, REQ-034-001).
# Served by GET /download/{slug}/cli — run with:
#   curl -fsSL __API_BASE__/download/__SLUG__/cli | bash
# Installs a governed Hermes CLI bound to your organization: Hermes CLI +
# trust-agent-governance plugin + org binding + white-label skin + tenant
# model gateway. Zero Hermes core patches. Targets: Linux/macOS/WSL2.
set -euo pipefail

SLUG="__SLUG__"
ORG_CODE="__SLUG__"
API_BASE="__API_BASE__"
BUNDLE_URL="${API_BASE}/download/${SLUG}/bundle"
LITELLM_BASE="__LITELLM_BASE__"
MODEL="__MODEL__"
PRODUCT_NAME="__PRODUCT_NAME__"
SKIN="trust-__SLUG__"

HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --home) HOME_DIR="$2"; shift 2 ;;
    *) echo "Unknown option: $1 (use --home DIR)" >&2; exit 2 ;;
  esac
done
for tool in curl tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "ERROR: '$tool' is required but not installed." >&2
    exit 1
  }
done

mkdir -p "$HOME_DIR"
HOME_DIR="$(cd "$HOME_DIR" && pwd)"
export HERMES_HOME="$HOME_DIR"

echo "==> Installing ${ORG_CODE} governed CLI into ${HOME_DIR}"

# 1. Hermes CLI — official installer when missing (D-023-04).
if ! command -v hermes >/dev/null 2>&1; then
  echo "==> ${PRODUCT_NAME} not found — installing…"
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
  command -v hermes >/dev/null 2>&1 || {
    echo "ERROR: ${PRODUCT_NAME} installed but not on PATH." >&2
    echo "Open a new terminal (PATH refresh) and re-run this installer." >&2
    exit 1
  }
else
  echo "==> ${PRODUCT_NAME} found: $(hermes --version 2>/dev/null | head -1)"
fi

# 1b. trust-agent command alias (white-label, REQ-PE-003): the employee types
#     trust-agent, not hermes. Wrapper in ~/.local/bin — cero parches al core.
ALIAS_DIR="${HOME}/.local/bin"
mkdir -p "$ALIAS_DIR"
cat > "$ALIAS_DIR/trust-agent" <<'ALIAS_EOF'
#!/usr/bin/env bash
exec hermes "$@"
ALIAS_EOF
chmod +x "$ALIAS_DIR/trust-agent"

# 2. Canonical bundle = single source of the plugin, trust-login and the
#    white-label skin (D-034-02). One GET; the served sha256 travels in the
#    X-Bundle-SHA256 response header and is verified locally.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
echo "==> Fetching the ${ORG_CODE} bundle (${BUNDLE_URL})"
curl -fsSL -D "$WORK/headers" "$BUNDLE_URL" -o "$WORK/bundle.tar.gz"
EXPECTED_SHA="$(tr -d '\r' < "$WORK/headers" | awk -F': ' \
  'tolower($1)=="x-bundle-sha256" {print $2}')"
ACTUAL_SHA="$(sha256sum "$WORK/bundle.tar.gz" 2>/dev/null | cut -d' ' -f1 \
  || shasum -a 256 "$WORK/bundle.tar.gz" | cut -d' ' -f1)"
if [[ -n "$EXPECTED_SHA" && "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
  echo "ERROR: bundle checksum mismatch (expected $EXPECTED_SHA, got $ACTUAL_SHA)." >&2
  exit 1
fi
ROOT="trust-agent-${SLUG}"
tar -xzf "$WORK/bundle.tar.gz" -C "$WORK" \
  "$ROOT/plugin" "$ROOT/trust-login" "$ROOT/skin.yaml"

# 3. Governance plugin (verbatim copy of the converged tree).
echo "==> Installing trust-agent-governance plugin"
mkdir -p "${HOME_DIR}/plugins"
rm -rf "${HOME_DIR}/plugins/trust-agent-governance"
cp -r "${WORK}/${ROOT}/plugin/trust-agent-governance" \
      "${HOME_DIR}/plugins/trust-agent-governance"
find "${HOME_DIR}/plugins/trust-agent-governance" -name '__pycache__' \
  -type d -exec rm -rf {} + 2>/dev/null || true

# 4. Organization binding (org_code is PUBLIC, D-023-02; the security of
#    enrollment is the admin approval, spec 007).
echo "==> Binding this CLI to organization ${ORG_CODE}"
mkdir -p "${HOME_DIR}/trust-agent"
chmod 700 "${HOME_DIR}/trust-agent"
printf '%s\n' "${ORG_CODE}" > "${HOME_DIR}/trust-agent/org-code.txt"

# 5. Environment: control plane + org code + device type for the plugin's
#    zero-UI auto-enroll (spec 034: devices enrolled as type 'cli').
ENV_FILE="${HOME_DIR}/.env"
touch "${ENV_FILE}"; chmod 600 "${ENV_FILE}"
set_env() {  # set_env KEY VALUE — upsert one line
  sed -i.bak "/^${1}=/d" "${ENV_FILE}" && rm -f "${ENV_FILE}.bak"
  printf '%s=%s\n' "$1" "$2" >> "${ENV_FILE}"
}
set_env TRUST_AGENT_API "${API_BASE}"
set_env TRUST_ORG_CODE "${ORG_CODE}"
set_env TRUST_DEVICE_TYPE "cli"

# 6. White-label skin from the organization's published branding.
echo "==> Applying ${ORG_CODE} white-label skin"
mkdir -p "${HOME_DIR}/skins"
cp "${WORK}/${ROOT}/skin.yaml" "${HOME_DIR}/skins/${SKIN}.yaml"

# 7. Profile config — via the safe writer, never a hand edit (invariant).
echo "==> Configuring the agent (gateway standalone, plugin, skin, model)"
hermes config set gateway.standalone true --force
hermes config set plugins.enabled '["trust-agent-governance"]' --force
hermes config set platform_toolsets.cli '["hermes-cli","trust_agent"]' --force
hermes config set display.skin "${SKIN}" --force
hermes config set model.default "${MODEL}" --force
hermes config set model.provider custom --force
hermes config set model.base_url "${LITELLM_BASE}" --force
hermes config set telemetry.shared_metrics.enabled false --force

# 8. Login helper on PATH of the home.
cp "${WORK}/${ROOT}/trust-login" "${HOME_DIR}/trust-login"
chmod +x "${HOME_DIR}/trust-login"

cat <<NEXT

==> Installation complete — governed CLI for organization ${ORG_CODE}.

Next steps:
  1. Sign in with your organization account (SSO + MFA):
       ${HOME_DIR}/trust-login
     On a headless server, run it from a machine with a browser and copy
     the resulting trust-agent/id-token.txt over (0600), or use SSH port
     forwarding. This helper also provisions your model key.
  2. Start the agent:
       trust-agent
     Your device is enrolled automatically on first run (no enrollment
     screens). An organization admin approves it, and from then on the
     agent runs under your organization's governance policies.

NEXT
"""


def build_cli_installer(
    *,
    slug: str,
    api_base: str,
    litellm_base_url: str,
    model: str,
    product_name: str = "Trust Agent",
) -> bytes:
    """-> bytes del script bash (ejecutable, con org_code embebido).

    `api_base` es el origen desde el que el empleado descargó (D-023-06) —
    el mismo criterio del binding del bundle desktop.
    """
    script = (
        _CLI_INSTALL_SH
        .replace("__SLUG__", slug)
        .replace("__API_BASE__", api_base)
        .replace("__LITELLM_BASE__", litellm_base_url)
        .replace("__MODEL__", model)
        .replace("__PRODUCT_NAME__", product_name)
    )
    return script.encode()

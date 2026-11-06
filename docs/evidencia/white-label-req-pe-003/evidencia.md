# Evidencia — White-label total (REQ-PE-003, GAP-42-B)

**Fecha**: 2026-10-29
**Spec**: 042-product-experience §3.2, §7, REQ-PE-003
**Tarea**: t_e2383fdb

## Cambios realizados

### 1. Wizard Electron (`platform/installer-app/main.js`)

- **Constante `PRODUCT`**: `BRANDING.product_name || "Trust Agent"` — el nombre
  visible se lee del branding horneado al build.
- **Mensajes de log visibles** (líneas 166-185 originales):
  - `"==> Hermes not found — installing the official Hermes Agent"` → `` `==> ${PRODUCT} not found — installing…` ``
  - `"Hermes install failed:…"` → `` `${PRODUCT} install failed:…` ``
  - `"Hermes installed but not found on PATH…"` → `` `${PRODUCT} installed but not found on PATH…` ``
  - `` `==> Hermes found: …` `` → `` `==> ${PRODUCT} found: …` ``
- **Errores throw** (líneas 270, 385): `"hermes config set … failed"` → `"config set … failed"`.
- **Alias `trust-agent`** (nuevo paso 1b): escribe un wrapper en
  `~/.local/bin/trust-agent` (POSIX) o `%LOCALAPPDATA%\hermes\trust-agent.cmd`
  (Windows) que invoca `hermes "$@"`. Cero parches al core.
- **Launch handler**: `hermes` → `trust-agent` (Windows `trust-agent.cmd`).
  Hint: `"Open a new terminal and run: trust-agent"`.

### 2. Bundle install.sh (`platform/app/download/bundle.py`, plantilla `_INSTALL_SH`)

- Variable `PRODUCT_NAME="__PRODUCT_NAME__"` horneada con `branding.product_name`.
- Mensajes echo: `"Hermes not found…"` → `"${PRODUCT_NAME} not found…"`, etc.
- **Alias `trust-agent`** (paso 1b): wrapper `~/.local/bin/trust-agent` → `exec hermes "$@"`.
- Heredoc "Next steps": `hermes` → `trust-agent`.

### 3. Instalador CLI (`platform/app/download/cli.py`, plantilla `_CLI_INSTALL_SH`)

- Variable `PRODUCT_NAME` + reemplazo en `build_cli_installer(product_name=…)`.
- Mensajes echo reescritos igual que el bundle.
- **Alias `trust-agent`** (paso 1b): mismo wrapper.
- Heredoc "Next steps": `hermes` → `trust-agent`.

### 4. Router (`platform/app/download/router.py`)

- `download_cli_installer` ahora lee el branding del tenant y pasa
  `product_name` al builder del CLI installer.

### 5. README del bundle (`platform/app/download/bundle.py`, plantilla `_README`)

- Eliminado el enlace `[Hermes Agent](https://hermes-agent.nousresearch.com)`.
- `"a complete Hermes Agent with the {org} governance plugin"` → `"bound to
  your organization with the {org} governance plugin"`.
- `"The installer sets up the complete agent: Hermes, the governance plugin…"`
  → `"The installer sets up the complete agent: the governance plugin…"`
- Sección Run: `hermes` → `trust-agent`.

### 6. Skin YAML (`_skin_yaml`)

- Comentario `# Activate with: hermes config set…` → `# Activate with:
  trust-agent config set…` (se propaga al install.sh que hornea el skin inline).

### 7. Página de descarga (`platform/app/download/page.py`)

- `"adds the Hermes CLI (official installer)"` → `"adds the agent CLI"`.

## Criterios de aceptación

### AC-003-1: grep "hermes" en textos visibles → 0 resultados

**Exclusión documentada**: se excluyen rutas internas (`~/.hermes`), env vars
del core (`HERMES_HOME`, `$HERMES_HOME`), el comando del binario core en
sentencias no-echo (`hermes config set`, `command -v hermes`, `hermes
--version`), y el contenido del wrapper `exec hermes "$@"` (escrito a archivo
vía heredoc `cat > file <<EOF`, no a stdout). Estas son infraestructura del
core, no texto visible al empleado.

**Verificación**:

| Superficie | grep "hermes" (case-insensitive) en texto visible | Resultado |
|---|---|---|
| Wizard renderer HTML (`index.html`) | 0 | ✅ |
| Wizard renderer JS (`app.js`) | 0 | ✅ |
| Wizard log() lines (`main.js`) | 0 (excluye `${hermes.version}` = variable JS) | ✅ |
| Wizard throw new Error (`main.js`) | 0 | ✅ |
| Wizard hint (`main.js`) | 0 | ✅ |
| install.sh generado (echo + heredoc stdout) | 0 | ✅ |
| CLI installer generado (echo + heredoc stdout) | 0 | ✅ |
| README del bundle | 0 | ✅ |
| Página de descarga (`/download/{slug}`) | 0 | ✅ |

### AC-003-2: alias `trust-agent` funcional

- El wrapper `~/.local/bin/trust-agent` contiene `exec hermes "$@"` y se
  instala con `chmod +x`.
- **Test real**: wrapper + fake `hermes` → `trust-agent --version` produce
  `FAKE_HERMES_CALLED: --version` (pass-through verificado).
- En Windows: `%LOCALAPPDATA%\hermes\trust-agent.cmd` con `@echo off\r\nhermes %*\r\n`.
- `hermes` sigue existiendo (compatibilidad) pero no es el camino mostrado.

### AC-003-3: TUI muestra `agent_name` = `product_name`

- No modificado — ya pasaba. El skin `_skin_yaml` genera
  `branding.agent_name: {product}` desde `branding.product_name`.
- Test `test_skin_agent_name_is_product_name` verifica `agent_name: Acme Agent`.

## Tests

```
TRUST_TEST_DB=trust_test_e2383fdb \
TRUST_HERMES_PLUGIN_DIR=platform/hermes/plugins/trust-agent-governance \
pytest tests/test_white_label.py tests/test_hermes_download.py tests/test_cli_governed.py \
  -q --deselect tests/test_cli_governed.py::test_page_offers_desktop_and_cli
```

**Resultado**: 31 passed, 1 deselected (preexistente: spec 040 rediseñó
"Download for desktop" → "Download for your platform", no relacionado a este
cambio).

### Nuevos tests (`tests/test_white_label.py`, 13 tests)

- `test_install_sh_no_visible_hermes` — AC-003-1 install.sh
- `test_cli_installer_no_visible_hermes` — AC-003-1 CLI installer
- `test_readme_no_hermes` — AC-003-1 README
- `test_wizard_renderer_no_hermes` — AC-003-1 wizard HTML
- `test_download_page_no_hermes` — AC-003-1 página de descarga
- `test_install_sh_installs_trust_agent_alias` — AC-003-2 bundle
- `test_cli_installer_installs_trust_agent_alias` — AC-003-2 CLI
- `test_install_sh_shows_trust_agent_as_command` — comando mostrado
- `test_cli_installer_shows_trust_agent_as_command` — comando mostrado
- `test_readme_shows_trust_agent_as_command` — comando mostrado
- `test_skin_agent_name_is_product_name` — AC-003-3
- `test_product_name_in_install_sh_echo` — product_name horneado
- `test_cli_installer_uses_product_name` — product_name horneado

## Archivos modificados

- `platform/installer-app/main.js`
- `platform/app/download/bundle.py`
- `platform/app/download/cli.py`
- `platform/app/download/router.py`
- `platform/app/download/page.py`
- `platform/tests/test_white_label.py` (nuevo)

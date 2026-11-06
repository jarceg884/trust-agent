# Instaladores desktop por plataforma — Spec 040 (t_fff52ec1)

> **Mandato Miller (2026-10-29)**: "No debería ser un .exe para Windows,
> luego Mac y finalmente Linux — al estilo que hace Hermes en su página?"
>
> La página de descarga de cada org (`GET /download/{slug}`) ofrece ahora
> **instaladores por plataforma** — botón Windows (`.exe` NSIS), botón macOS,
> botón Linux (AppImage) — además del CLI (`curl … | bash`, spec 034) y del
> bundle tarball (spec 023, ahora opción avanzada). Detección de OS del
> visitante que resalta su botón. Nada simulado: macOS sin runner es un
> **gap documentado** con plan, no un botón falso.

Spec: `specs/040-platform-installers/` · Evidencia:
`docs/evidencia/desktop-installers/` · Tests:
`platform/tests/test_platform_installers.py`.

## 1. Qué es el instalador

**Trust Agent Setup** (`platform/installer-app/`): una app Electron real
empaquetada con electron-builder, **por tenant**. El build hornea DENTRO del
instalador (D-040-02):

- `resources/org-binding.json` — slug de la org, URL del control plane,
  gateway del tenant, issuer/client OIDC, versión del plugin.
- `resources/branding.json` — branding publicado (nombre, tagline, accent,
  logo como data URI).
- Iconos generados desde el branding (accent + inicial del producto).

El empleado doble-clic-instala y el wizard (zero config):

1. **Instala Hermes COMPLETO** por la vía oficial de cada plataforma
   (Windows nativo: `install.ps1` sin admin → `%LOCALAPPDATA%\hermes`;
   Linux/macOS: `install.sh`). Si Hermes ya existe, lo reutiliza.
2. **Descarga el bundle canónico** `GET /download/{slug}/bundle` (sha256
   verificado — fuente única del plugin + skin, patrón D-034-02) e instala
   el plugin `trust-agent-governance`, el binding (`org-code.txt`), el
   `.env` (`TRUST_AGENT_API`/`TRUST_ORG_CODE`), el skin white-label y la
   config del agente (gateway standalone, plugin, toolset, modelo).
3. **Sign-in OIDC embebido** (D-040-09): BrowserWindow contra el realm del
   tenant (authorization-code + PKCE S256, `acr_values=2`), escribe
   `id-token.txt` (0600) y auto-emite la virtual key. En Windows el
   empleado NO necesita Python — el wizard ES el login helper.
4. **Done** → el agente arranca; el auto-enroll zero-UI (spec 022) enrola
   el dispositivo en el primer arranque y el admin lo aprueba (spec 007).

Modos headless reales (E2E/admin): `TrustAgentSetup --install-only`
(instala y sale) y `--status` (imprime el binding horneado).

## 2. Realidad de build (evidencia en `docs/evidencia/desktop-installers/`)

| Plataforma | ¿Desde este servidor Linux? | Cómo |
|---|---|---|
| **Windows .exe (NSIS x64)** | ✅ | electron-builder **26.18.0** (pinneado) + **wine64 + wine32:i386** (extrae el uninstaller). El alpha 27 tiene el flujo NSIS-Linux roto (path `Z:\` al makensis nativo) — hallazgo documentado |
| **Linux AppImage x64** | ✅ | electron-builder, sin requisitos extra |
| **macOS .dmg** | ❌ | Requiere macOS (firma/notarización + dmg). **Gap**: workflow `.github/workflows/desktop-installers-macos.yml` (runner macos-14, `workflow_dispatch` con slug/api-base) listo en el repo; el botón macOS de la página ofrece la vía CLI real de hoy y el endpoint responde 404 `installer_not_built` — jamás un archivo fingido |

Requisitos del host de build: node 22 + npm, wine64 + wine32:i386
(`dpkg --add-architecture i386 && apt install wine32 wine64`), el venv de
platform (psycopg, Pillow).

## 3. Pipeline de build y serving

```
HOST                                CONTROL PLANE (api :8000)
────                                ─────────────────────────
scripts/build_desktop_installers.py
  --slug {slug} [--api-base ORIGEN]
  1. lee tenant + branding publicado (DB canónica)
  2. hornea org-binding.json + branding.json + iconos
  3. electron-builder → .exe / .AppImage      GET /download/{slug}
  4. installer-artifacts/{slug}/{ver}/            página: 3 botones por
     + installer-manifest.json                    plataforma + CLI +
                                                  bundle (avanzado)
                                               GET /download/{slug}/installer/{windows|linux|macos}
                                                 200 binario + audit
                                                 download.issued
                                                 artifact=installer-{platform}
                                                 404 installer_not_built (honesto)
```

- **Versionado (D-040-07)**: artefactos
  `trust-agent-setup-{slug}-v{plugin_version}-{os}-{arch}.{ext}`; solo se
  sirve el build de la versión del plugin ACTUAL (`plugin.yaml`).
- **Seguridad (REQ-PI-004)**: heredada del bundle — rate limit por IP+slug,
  anti-enumeración (slug desconocido quema el contador de IP), fail-closed
  (suspendido → 403, link disabled → 403 `link_disabled`).
- **Compose**: el api monta `./installer-artifacts` read-only
  (`TRUST_INSTALLER_ARTIFACTS_DIR` overrideable).

## 4. E2E real (tenant `pi-e2e`)

Script: `platform/scripts/evidence_desktop_installers.py` (fases setup →
build → serve → appimage → gui → firstrun → approve → governed).
Resultados y artefactos: `docs/evidencia/desktop-installers/`
(logs numerados, HTML de la página, screenshots del wizard, manifest).

## 5. Entrega del .exe a Miller (tester) — handoff honesto

1. Descargar: `GET /download/{slug}/installer/windows` (o botón Windows de
   la página). Verificar sha256 contra `installer-manifest.json`.
2. **Gap conocido**: el build actual hornea el origen dev-local
   (`http://127.0.0.1:8000`). Para instalar en la laptop de Miller, el
   stack (api :8000 + Keycloak :8080) debe ser alcanzable desde su máquina
   y el instalador se reconstruye con
   `scripts/build_desktop_installers.py --slug {slug} --api-base <origen-público>`
   (un comando, ~8 min). Tarea de seguimiento creada para el despliegue.
3. Sin firma Authenticode (gap documentado): Windows SmartScreen mostrará
   advertensa en la primera instalación ("More info → Run anyway").
4. macOS: hoy la vía real para un empleado con Mac es el CLI
   (`curl -fsSL <origen>/download/{slug}/cli | bash`, soporta macOS); el
   .dmg requiere ejecutar el workflow con runner macos-14.

## 6. Regresión y convenciones

- Tests: `platform/tests/test_platform_installers.py` (página, serving,
  audit por plataforma, honestidad macOS, versionado, seguridad pública,
  aislamiento) — suite de descarga 023 sin regresión.
- La página pública sigue el estándar ui-standard.md (lucide inline,
  `ta-rise`, `focus-visible`, branding publicado inline).
- `installer-artifacts/` y `platform/installer-app/{resources,build}/`
  están en `.gitignore` (binarios grandes + binding por tenant — jamás al
  repo).

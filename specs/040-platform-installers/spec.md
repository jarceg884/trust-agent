# Spec 040 — Instaladores desktop por plataforma: Windows .exe + macOS + Linux al estilo de la página de Hermes

| Campo | Valor |
|---|---|
| **Estado** | ✅ CONVERGIDA (implementada y validada con evidencia real 2026-10-29 — docs/desktop-installers.md; AC-PI-001..008 verificados en vivo: página 3 botones + OS detect, .exe 102MB PE real servido, AppImage 116MB servido, macOS 404 honesto, audit installer-{platform}, tests 13/13 PASS, spec 023 sin regresión 9/9) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-29 |
| **Autor** | dev (tarea t_fff52ec1) |
| **Padre** | specs/023-hermes-download (página pública + bundle + link por tenant); specs/034-cli-governed (instalador CLI, patrón D-034-02 fuente única); specs/022-hermes-real-bridge (plugin trust-agent-governance); specs/028-branding-versioning (branding publicado) |
| **Idioma** | Español (docs) / inglés (código, UI copy) |

---

## 1. Problema (mandato Miller)

> "No debería ser un .exe para Windows, luego Mac y finalmente Linux — al estilo que
> hace Hermes en su página?"

Hoy `/download/{slug}` ofrece un **tarball** (bundle dev-local Linux) y un **instalador
CLI** bash (Linux/macOS/WSL2). No hay instaladores desktop **por plataforma**: un
empleado con Windows no tiene un `.exe` que doble-clic-instalar, y uno con Linux no
tiene un AppImage. La página oficial de Hermes ofrece botones por plataforma; la
página de descarga de cada org debe hacer lo mismo con el agente gobernado de la org.

Esta spec es dueña de:

1. **La página de descarga rediseñada**: 3 botones grandes por plataforma
   (Windows `.exe` / macOS / Linux AppImage) + la sección CLI existente, con
   detección de OS del visitante y branding del tenant.
2. **El instalador desktop por plataforma** (app Electron empaquetada con
   electron-builder) con el org binding + control plane URL + branding horneados
   DENTRO del instalador: el empleado instala y ya está enrolado a la org (zero config).
3. **La realidad de build documentada con evidencia**: qué se puede compilar real
   desde este servidor Linux y qué no (macOS), con decisión y plan.
4. **El E2E real** con evidencia en `docs/evidencia/desktop-installers/`.
5. **Versionado** por release del plugin + audit `download.issued` por plataforma.

## 2. Realidad de build (evidencia recolectada 2026-10-29, ANTES de decidir)

El servidor de build (este host Linux, Ubuntu 24.04, kernel 6.8) tiene Node v22.22.2,
npm 10.9.7 y acceso a red (github.com y registry.npmjs.org responden 200). Hermes
está instalado en `/usr/local/lib/hermes-agent` (git) y su desktop es Electron
(`apps/desktop`, electron 40.10.2, electron-builder 27.0.0-alpha.6).

Smoke test real (app mínima + electron-builder + electron 40.10.2, artefactos
en `docs/evidencia/desktop-installers/`):

| Target | ¿Compila desde este Linux? | Evidencia |
|---|---|---|
| **Linux AppImage (x64)** | ✅ SÍ | `EBSmoke-0.0.1.AppImage` (109 MB, ELF x86-64 real) construido en este host |
| **Windows NSIS .exe (x64)** | ✅ SÍ, **con wine (wine64 + wine32:i386)** | electron-builder ejecuta el instalador una vez bajo wine para extraer el uninstaller. Hallazgos reales: (a) sin wine → `spawn wine ENOENT`; (b) solo `wine64` (Ubuntu) → `failed to load syswow64\ntdll.dll` — se requiere `dpkg --add-architecture i386 && apt install wine32 wine64`; (c) el "toolset wine portable" de electron-builder v27 (wine@1.0.1) está INCOMPLETO para Linux (sin PEs i386/x86_64-windows) y su wine no ejecuta PEs; (d) electron-builder **27.0.0-alpha.6 tiene el flujo NSIS-Linux roto** (pasa el path `Z:\` al makensis nativo wfwns, que no lo abre) — se PINNEA **26.18.0** (estable), cuyo flujo estándar funciona. Smoke: `EBSmoke Setup 0.0.1.exe` (102 MB, PE real, uninstaller extraído) |
| **macOS .dmg** | ❌ NO | electron-builder requiere macOS para firmar/notarizar y crear .dmg; desde Linux no se produce un .dmg real. **No se finge** (constitution I). Plan: workflow `desktop-installers-macos.yml` (runner macos-14) listo en el repo |

Además (docs de Hermes, `apps/desktop/BUILDING.md`): el desktop oficial de Hermes
usa **MSIX** en Windows (no NSIS/MSI) y se construye en runners Windows/macOS; el
MSIX requiere Windows 11 22H2+. Repackear el MSIX de Hermes por tenant no es viable
desde este servidor ni deseable (firma de Nous Research, floor Win11).

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| **D-040-01** | **El instalador por plataforma es una app Electron propia** (`platform/installer-app/`, "Trust Agent Setup"): un wizard de enrolamiento del empleado, empaquetado con electron-builder. NO es un repack del MSIX de Hermes (D-§2: firma ajena, floor Win11, se construye en Windows). El wizard instala un Hermes COMPLETO por la vía oficial de cada plataforma (Windows nativo: `install.ps1` oficial, sin admin, `%LOCALAPPDATA%\hermes`; Linux/macOS: `install.sh` oficial), luego aplica plugin + binding + skin + config y hace el sign-in OIDC embebido. Es "el instalador del Hermes desktop de la org": al terminar, el empleado tiene su Hermes (CLI/TUI/desktop) enrolado y gobernado. | Mandato Miller; docs Hermes windows-native.md; constitution IV (cero parches al core) |
| **D-040-02** | **Binding horneado al build, por tenant**: cada build del instalador recibe `resources/org-binding.json` (slug, api_base, litellm_base_url, model, oidc_issuer_url, oidc_client_id, device_type) + `resources/branding.json` (branding publicado) inyectados por el script de build. El empleado instala y ya está bound — zero config. org_code es público (D-023-02); el instalador NO lleva secretos (el id_token lo produce el sign-in OIDC después de instalar). | Body de la tarea; D-023-02 |
| **D-040-03** | **Fuente única del plugin = bundle canónico** (patrón D-034-02): el wizard descarga `GET /download/{slug}/bundle` del control plane, verifica el `X-Bundle-SHA256` localmente y extrae `plugin/` + `skin.yaml`. El instalador NO duplica el árbol del plugin: siempre instala la versión del release actual. | Spec 034 D-034-02 |
| **D-040-04** | **macOS = gap documentado con plan, no simulado**: desde Linux no se construye un .dmg real. El repo incluye el workflow `/.github/workflows/desktop-installers-macos.yml` (runner `macos-14`, `workflow_dispatch` con inputs slug/api-base) que construye el mismo `installer-app` con target dmg — listo para ejecutar cuando se habilite el runner. La página muestra el botón macOS de forma HONESTA: si no hay .dmg servido, el botón enlaza a las instrucciones CLI para macOS (soportadas hoy) con la nota del runner. `GET /download/{slug}/installer/macos` responde 404 `installer_not_built` (contrato honesto, nunca un archivo falso). | Body de la tarea ("NO fingir un .dmg"); constitution I |
| **D-040-05** | **Build host-side, serving desde el control plane**: el build corre en el HOST (requiere node 22 + electron-builder + red; el contenedor api no los tiene). `scripts/build_desktop_installers.py` orquesta: lee tenant + branding publicado de la DB canónica, inyecta los resources, corre electron-builder y deja los artefactos en `installer-artifacts/{slug}/{plugin_version}/` (volume `:ro` al api). El api los sirve con `GET /download/{slug}/installer/{platform}` — misma seguridad que el bundle: rate limit, anti-enumeración, fail-closed (suspendido/disabled) y audit. | Constitution VII (dev-local); D-023-07 |
| **D-040-06** | **Página estilo Hermes**: 3 botones grandes por plataforma con detección de OS (user-agent) que resalta el botón del visitante, más la sección CLI actual intacta. La disponibilidad por plataforma se lee del directorio de artefactos al render (server-side): botón habilitado solo si hay build servido; macOS muestra el estado honesto (D-040-04). Iconografía lucide inline, estándar ui-standard.md, UI copy inglés. | Mandato Miller; D-023-11 |
| **D-040-07** | **Versionado por release del plugin**: artefactos nombrados `{product}-{slug}-v{plugin_version}-{platform}-{arch}.{ext}`; junto a cada artefacto viaja `installer-manifest.json` (sha256, size, filenames, plugin_version, built_at, git rev). El endpoint sirve SIEMPRE el build de la versión del plugin actual del árbol (`plugin.yaml`): si el directorio de la versión actual no existe → 404 `installer_not_built` (nunca sirve un build viejo sin decirlo; la página muestra "build in progress / not built"). | Body de la tarea §5 |
| **D-040-08** | **Audit por plataforma**: `download.issued` con payload `{artifact: "installer-windows"|"installer-linux"|"installer-macos", platform, filename, installer_sha256, plugin_version, ip, user_agent}` — mismo patrón del CLI (spec 034, artifact=cli). Sin acción nueva: el enum de audit es extensible por datos y `download.issued` ya es el evento de emisión. | Spec 008/023 D-023-10; spec 034 REQ-034-002 |
| **D-040-09** | **Sign-in OIDC embebido en el wizard** (reemplaza a `trust-login` en el flujo desktop): BrowserWindow embebida contra el realm del tenant (authorization-code + PKCE S256, callback `127.0.0.1:8765`, `acr_values=2`), escribe `$HERMES_HOME/trust-agent/id-token.txt` (0600) y auto-emite la virtual key (`POST /api/auth/session` → `POST /api/models/keys`) guardándola con `hermes config set model.api_key`. En Windows nativo el empleado NO necesita Python: el wizard es el login helper. Jamás maneja passwords ni códigos MFA (los teclea en el IdP). | D-023-13; spec 022 D-HRB-01 |
| **D-040-10** | **E2E**: (a) AppImage ejecutado EN ESTE SERVIDOR con el flujo completo real (instalación → binding → sign-in → auto-enroll → aprobación → sesión gobernada), evidencia con logs y capturas; (b) el `.exe` de Windows se entrega a Miller (él es el tester: lo instala en su laptop) — handoff documentado con sha256 e instrucciones; verificación local del .exe = extracción NSIS (7z) + estructura esperada. Nada simulado. | Body de la tarea §4 |

## 4. Requisitos (formato EARS) — serie REQ-PI

- **REQ-PI-001** (página por plataforma): Cuando un navegador anónimo solicite `GET /download/{slug}` con tenant activo y link habilitado, la página mostrará 3 botones de plataforma (Windows `.exe`, macOS, Linux AppImage) con el branding del tenant, resaltando el botón del OS del visitante (user-agent), además de la sección CLI actual.
- **REQ-PI-002** (botones honestos): Cuando no exista build servido para una plataforma, su botón lo reflejará honestamente (estado "not built yet" con fallback a la vía CLI) y nunca enlazará a un artefacto inexistente.
- **REQ-PI-003** (serving real): Cuando se solicite `GET /download/{slug}/installer/{windows|linux|macos}` y exista build de la versión actual del plugin, el sistema responderá 200 con el binario real y audit `download.issued` con `artifact=installer-{platform}`; si no existe, 404 `installer_not_built`.
- **REQ-PI-004** (seguridad pública): Los endpoints de instalador heredan la seguridad del bundle: rate limit por IP+slug, anti-enumeración (slug desconocido quema contador), fail-closed (suspendido → 403, disabled → 403 `link_disabled`).
- **REQ-PI-005** (binding horneado): Cuando el empleado ejecute el instalador de una org, el wizard quedará bound a esa org sin pedir configuración: escribe `org-code.txt`, `.env` (`TRUST_AGENT_API`, `TRUST_ORG_CODE`), instala el plugin desde el bundle canónico (sha256 verificado), aplica el skin white-label y la config del agente (gateway standalone, plugin, toolset, modelo del tenant).
- **REQ-PI-006** (instalación completa): Cuando el wizard corra en una máquina sin Hermes, instalará Hermes COMPLETO por la vía oficial de la plataforma (Windows: `install.ps1`; Linux/macOS: `install.sh`); si Hermes ya existe, lo reutiliza.
- **REQ-PI-007** (sign-in embebido): Cuando el empleado complete el sign-in en el wizard, existirá `$HERMES_HOME/trust-agent/id-token.txt` (0600) y la virtual key quedará configurada; el dispositivo se enrola automáticamente en el primer arranque del agente (zero-UI, spec 022) y queda gobernado tras la aprobación admin.
- **REQ-PI-008** (versionado): Cuando se sirva un instalador, su nombre y manifest llevarán la versión del plugin del release actual, y el audit registrará `plugin_version`.
- **REQ-PI-009** (macOS honesto): El sistema jamás servirá un `.dmg` que no fue construido en macOS; el botón macOS documenta la vía disponible hoy (CLI) y el plan del runner.
- **REQ-PI-010** (build reproducible): `scripts/build_desktop_installers.py --slug S --platforms windows,linux` producirá ambos artefactos desde un árbol limpio con un solo comando, dejando `installer-manifest.json` verificable.

## 5. Contratos

### 5.1 Endpoints públicos nuevos (mismo prefijo que spec 023)

```
GET /download/{slug}/installer/windows   → 200 application/octet-stream
                                            (.exe NSIS, Content-Disposition attachment,
                                             X-Installer-SHA256)
GET /download/{slug}/installer/linux     → 200 (.AppImage)
GET /download/{slug}/installer/macos     → 200 (.dmg) | 404 installer_not_built (hoy)
```

Errores: 404 `unknown_org` (anti-enumeración), 403 `tenant_suspended`,
403 `link_disabled`, 404 `installer_not_built`, 429 `rate_limited`.

### 5.2 `org-binding.json` (horneado en `resources/` del instalador)

```json
{
  "slug": "<tenant slug>",
  "api_base": "https://<control-plane-origin>",
  "litellm_base_url": "http://<gateway>",
  "model": "glm-5.2",
  "oidc_issuer_url": "https://<keycloak>/realms/<slug>",
  "oidc_client_id": "trust-agent-web",
  "device_type": "desktop",
  "plugin_version": "0.6.0"
}
```

### 5.3 `installer-manifest.json` (junto a los artefactos servidos)

```json
{
  "slug": "...", "plugin_version": "...", "built_at": "...", "git_rev": "...",
  "platforms": {
    "windows": {"filename": "...", "sha256": "...", "size": N},
    "linux":   {"filename": "...", "sha256": "...", "size": N}
  }
}
```

### 5.4 Audit payload (download.issued, artifact=installer-*)

`{slug, ip, user_agent, artifact: "installer-windows", platform: "windows",
filename, installer_sha256, plugin_version}`

## 6. Alcance y delimitación

| Componente | Dueño | Estado |
|---|---|---|
| Página pública rediseñada (3 botones + OS detect + CLI) | **esta spec** | esta corrida |
| `platform/installer-app/` (wizard Electron) | **esta spec** | esta corrida |
| `scripts/build_desktop_installers.py` (build host-side) | **esta spec** | esta corrida |
| Endpoints `/installer/{platform}` + audit | **esta spec** | esta corrida |
| Workflow GitHub Actions macOS (runner macos-14) | **esta spec** (archivo listo, ejecución pendiente de runner) | gap documentado |
| Bundle canónico / CLI installer / plugin / enroll | specs 023/034/022 | convergidos — esta spec los CONSUME |
| Firma de código Windows (Authenticode) | fuera de alcance (dev-local; documentado como gap) | — |
| MSIX Store de Hermes | fuera de alcance (propietario de Nous Research) | — |

## 7. Criterios de aceptación (AC-PI)

- **AC-PI-001**: `GET /download/{slug}` muestra los 3 botones por plataforma con branding y el botón del OS del visitante resaltado (evidencia HTML + screenshot).
- **AC-PI-002**: `GET /download/{slug}/installer/linux` sirve el AppImage real (200, sha256 header) y audita `download.issued artifact=installer-linux` (+1).
- **AC-PI-003**: `GET /download/{slug}/installer/windows` sirve el .exe real (200) y audita `artifact=installer-windows`; el .exe extraído con 7z contiene la app Electron + `org-binding.json` horneado del tenant.
- **AC-PI-004**: `installer/macos` sin build → 404 `installer_not_built` (honesto); slug desconocido → 404 + contador de IP; disabled → 403.
- **AC-PI-005**: AppImage ejecutado en este servidor: wizard completo → home de Hermes con plugin, binding, skin, config; sign-in OIDC real; auto-enroll real; aprobación admin; sesión gobernada (evidencia en `docs/evidencia/desktop-installers/`).
- **AC-PI-006**: El .exe de Windows entregado a Miller con sha256 + instrucciones (handoff documentado; Miller es el tester).
- **AC-PI-007**: `scripts/build_desktop_installers.py` reproduce el build completo de ambas plataformas en un comando (REQ-PI-010).
- **AC-PI-008**: Suite pytest: tests nuevos de endpoints/audit/página pasan; suite existente sin regresión (aislada con TRUST_TEST_DB propio).

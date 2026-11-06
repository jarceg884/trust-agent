# Evidencia — Spec 040: instaladores desktop por plataforma (t_fff52ec1)

> Realidad de build verificada el 2026-10-29 en ESTE servidor Linux
> (Ubuntu 24.04, kernel 6.8, Node v22.22.2, npm 10.9.7) antes de decidir
> (spec 040 §2). Nada simulado: cada fila es un comando ejecutado y su
> output real.

## 1. Realidad de build por plataforma (smoke test)

App mínima Electron (`main.js` + `index.html`) + electron-builder,
electron 40.10.2 (la del desktop de Hermes).

| Target | Resultado | Detalle real |
|---|---|---|
| **Linux AppImage x64** | ✅ construido | `EBSmoke-0.0.1.AppImage`, 108.964.527 bytes, `file` = ELF 64-bit LSB pie executable x86-64. sha256 `080f4106…48dc9` |
| **Windows NSIS x64** | ✅ construido (con wine) | `EBSmoke Setup 0.0.1.exe`, 102.869.182 bytes, firma PE (`MZ`), uninstaller extraído bajo wine. sha256 `2b643ce8…7c1e`. Log: `01-smoke-nsis-v26-build.log` |
| **macOS .dmg** | ❌ NO construible | electron-builder requiere macOS (firma/notarización + creación del dmg). Desde Linux no existe vía real. Plan: `.github/workflows/desktop-installers-macos.yml` (runner macos-14), listo en el repo, pendiente de ejecución |

### Hallazgos del camino (todos reproducibles)

1. **Sin wine**: `spawn wine ENOENT` — electron-builder ejecuta el
   instalador construido una vez bajo wine para extraer el uninstaller
   (`NsisTarget.js` → `WineVmManager`).
2. **Solo `wine64` (Ubuntu 24.04)**: `wine: failed to load
   \??\C:\windows\syswow64\ntdll.dll error c0000135` — falta el loader
   32-bit. Solución real aplicada:
   `dpkg --add-architecture i386 && apt install wine32 wine64`.
3. **Toolset wine portable de electron-builder v27 (wine@1.0.1)**: el
   bundle linux está incompleto para ejecutar PEs (sin i386 ni
   x86_64-windows; `wineboot.exe` falla con c0000135). Además el alpha
   **27.0.0-alpha.6** pasa el path estilo wine `Z:\…` al makensis NATIVO
   del bundle wfwns, que no puede abrirlo (`File: … no files found`) —
   el flujo NSIS-Linux del alpha está roto. **Decisión: pin
   electron-builder 26.18.0** (estable), cuyo flujo estándar
   (wine del host + makensis nativo + path nativo en el pase final)
   funciona — log `01-smoke-nsis-v26-build.log`, EXIT=0.
4. **Script NSIS custom** (`nsis.script`): alternativa investigada
   (salta el paso de wine), pero el template embebe el uninstaller con
   `File ${UNINSTALLER_OUT_FILE}` y sin wine no existe el archivo —
   descartada; la vía estándar con wine32 es la correcta.

## 2. E2E del flujo completo (tenant pi-e2e)

Fases del script `platform/scripts/evidence_desktop_installers.py`
(cada `--phase` en orden): setup → build → serve → appimage → gui →
firstrun → approve → governed. Resultados en
`docs/desktop-installers.md` §E2E y archivos numerados de este
directorio (logs, HTML de la página, screenshots del wizard, manifest).

## 3. Entrega del .exe a Miller (tester)

El `.exe` de Windows real se sirve desde el control plane:
`GET /download/{slug}/installer/windows` (binario ~100 MB, sha256 en el
header `X-Installer-SHA256` y en `installer-manifest.json`).
**Gap honesto**: el binding horneado apunta al origen dev-local
(`http://127.0.0.1:8000`). Para la prueba en la laptop de Miller el
stack (api :8000 + Keycloak :8080) debe ser alcanzable desde su máquina
y el instalador debe reconstruirse con `--api-base <origen-público>`
(un comando). Tarea de seguimiento creada para el despliegue.
